import { setTimeout } from "node:timers/promises";
import os from "node:os";
import Docker from "dockerode";
import {
  RedisCoordinator,
  AgentMessage,
  WorkerRequestResponse,
  utcNow,
} from "@agent-fleet/shared";

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

interface OrchestratorSettings {
  redisUrl: string;
  agentsImage: string;
  dockerNetwork: string;
  repoVolume: string;
  maxWorkersPerJob: number;
  cleanupDelaySeconds: number;
}

function settingsFromEnv(): OrchestratorSettings {
  return {
    redisUrl: process.env.REDIS_URL ?? "redis://redis:6379/0",
    agentsImage: process.env.AGENTS_IMAGE ?? "agentfleet-agents:latest",
    dockerNetwork: process.env.DOCKER_NETWORK ?? "agentfleet_default",
    repoVolume: process.env.REPO_VOLUME ?? "agentfleet_repo",
    maxWorkersPerJob: parseInt(
      process.env.MAX_WORKERS_PER_JOB ?? "6",
      10,
    ),
    cleanupDelaySeconds: parseFloat(
      process.env.CLEANUP_DELAY_SECONDS ?? "0",
    ),
  };
}

// ---------------------------------------------------------------------------
// Orchestrator
// ---------------------------------------------------------------------------

class Orchestrator {
  private readonly settings: OrchestratorSettings;
  private readonly coordinator: RedisCoordinator;
  private readonly docker: Docker;
  private readonly hostname: string;

  constructor(settings: OrchestratorSettings) {
    this.settings = settings;
    this.coordinator = new RedisCoordinator(settings.redisUrl);
    this.docker = new Docker({ socketPath: "/var/run/docker.sock" });
    this.hostname = os.hostname();
  }

  async start(): Promise<void> {
    await this.coordinator.ensureConsumerGroup("orchestrators");
    this.workerRequestLoop();

    while (true) {
      const messages = await this.coordinator.readJobs(
        "orchestrators",
        `orch-${this.hostname.slice(0, 8)}`,
        5000,
      );
      if (!messages) continue;

      for (const [, entries] of messages) {
        for (const [entryId, fields] of entries) {
          const raw =
            typeof fields === "object" && !Array.isArray(fields)
              ? (fields as Record<string, string>).data
              : Array.isArray(fields)
                ? fields[fields.indexOf("data") + 1]
                : undefined;
          if (!raw) {
            await this.coordinator.ackJob("orchestrators", entryId);
            continue;
          }

          let job: Record<string, unknown>;
          try {
            job = JSON.parse(raw) as Record<string, unknown>;
          } catch {
            await this.coordinator.ackJob("orchestrators", entryId);
            continue;
          }

          try {
            await this.spawnLeader(job);
            await this.coordinator.ackJob("orchestrators", entryId);
          } catch (err: unknown) {
            const jobId = String(job.job_id ?? job.id ?? "");
            if (jobId) {
              await this.coordinator.publishJobEvent(jobId, {
                type: "orchestrator_error",
                error: err instanceof Error ? err.message : String(err),
              });
            }
            await this.coordinator.ackJob("orchestrators", entryId);
          }
        }
      }
    }
  }

  // -------------------------------------------------------------------------
  // Spawn leader
  // -------------------------------------------------------------------------

  private async spawnLeader(job: Record<string, unknown>): Promise<void> {
    const jobId = String(job.job_id ?? job.id ?? "");
    if (!jobId) throw new Error("job_id missing");

    const prompt = String(job.prompt ?? "");
    await this.coordinator.upsertJob(jobId, {
      job_id: jobId,
      prompt,
      status: "queued",
      orchestrated: true,
    });

    const leaderId = `leader-${jobId.slice(0, 8)}`;
    console.log(
      `[orchestrator] spawning leader for job_id=${jobId}`,
    );

    const baseEnv = this.buildBaseEnv(jobId);
    await this.runContainer({
      name: `agentfleet-${jobId.slice(0, 8)}-leader`,
      env: { ...baseEnv, AGENT_ROLE: "leader", AGENT_ID: leaderId },
      labels: { "agentteam.job_id": jobId, "agentteam.role": "leader" },
    });

    await this.coordinator.publishJobEvent(jobId, {
      type: "leader_spawned",
      job_id: jobId,
      leader_id: leaderId,
    });

    this.cleanupWhenDone(jobId);
  }

  // -------------------------------------------------------------------------
  // Worker request loop
  // -------------------------------------------------------------------------

  private async workerRequestLoop(): Promise<void> {
    while (true) {
      try {
        const request = await this.coordinator.waitForWorkerRequest(5);
        if (!request) continue;

        const jobId = request.job_id;
        const leaderId = request.leader_id;
        const requested = request.requested_count;
        const granted = Math.min(
          requested,
          this.settings.maxWorkersPerJob,
        );

        console.log(
          `[orchestrator] spawning ${granted} workers for job_id=${jobId} (requested=${requested})`,
        );

        const baseEnv = this.buildBaseEnv(jobId);
        const workerIds: string[] = [];

        for (let i = 0; i < granted; i++) {
          const workerId = `worker-${jobId.slice(0, 8)}-${i}`;
          workerIds.push(workerId);
          await this.runContainer({
            name: `agentfleet-${jobId.slice(0, 8)}-worker-${i}`,
            env: {
              ...baseEnv,
              AGENT_ROLE: "worker",
              AGENT_ID: workerId,
            },
            labels: {
              "agentteam.job_id": jobId,
              "agentteam.role": "worker",
            },
          });
        }

        // Notify leader
        const response = WorkerRequestResponse.parse({
          job_id: jobId,
          granted_count: granted,
          worker_ids: workerIds,
          timestamp: utcNow(),
        });
        const msg = AgentMessage.parse({
          from_agent: "orchestrator",
          text: JSON.stringify(response),
          summary: `Granted ${granted} workers`,
        });
        await this.coordinator.sendMessage(jobId, leaderId, msg);

        await this.coordinator.publishJobEvent(jobId, {
          type: "workers_spawned",
          job_id: jobId,
          leader_id: leaderId,
          granted,
          worker_ids: workerIds,
        });
      } catch (err: unknown) {
        console.log(
          `[orchestrator] worker_request_loop error: ${err instanceof Error ? err.message : err}`,
        );
        await setTimeout(1000);
      }
    }
  }

  // -------------------------------------------------------------------------
  // Container management
  // -------------------------------------------------------------------------

  private buildBaseEnv(jobId: string): Record<string, string> {
    return {
      REDIS_URL: this.settings.redisUrl,
      AGENT_SDK_MODE: process.env.AGENT_SDK_MODE ?? "live",
      MODEL: process.env.MODEL ?? "",
      ANTHROPIC_API_KEY: process.env.ANTHROPIC_API_KEY ?? "",
      OPENAI_API_KEY: process.env.OPENAI_API_KEY ?? "",
      GOOGLE_API_KEY: process.env.GOOGLE_API_KEY ?? "",
      DRY_RUN_SLEEP_SECONDS:
        process.env.DRY_RUN_SLEEP_SECONDS ?? "0",
      JOB_ID: jobId,
      JOB_MODE: "ephemeral",
      MAX_WORKERS_PER_JOB: String(this.settings.maxWorkersPerJob),
    };
  }

  private async runContainer(opts: {
    name: string;
    env: Record<string, string>;
    labels: Record<string, string>;
  }): Promise<void> {
    // Remove existing container if present
    try {
      const existing = this.docker.getContainer(opts.name);
      await existing.remove({ force: true });
    } catch {
      // does not exist
    }

    const envList = Object.entries(opts.env).map(
      ([k, v]) => `${k}=${v}`,
    );

    const container = await this.docker.createContainer({
      Image: this.settings.agentsImage,
      name: opts.name,
      Env: envList,
      Labels: opts.labels,
      HostConfig: {
        NetworkMode: this.settings.dockerNetwork,
        Binds: [`${this.settings.repoVolume}:/repo:rw`],
        RestartPolicy: { Name: "" },
      },
    });
    await container.start();
  }

  private async cleanupWhenDone(jobId: string): Promise<void> {
    const terminal = new Set(["completed", "cancelled", "failed"]);
    let status = "";
    while (true) {
      const job = await this.coordinator.getJob(jobId);
      status = String(job?.status ?? "");
      if (terminal.has(status)) break;
      await setTimeout(3000);
    }

    if (this.settings.cleanupDelaySeconds > 0) {
      await setTimeout(this.settings.cleanupDelaySeconds * 1000);
    }

    const prefix = `agentfleet-${jobId.slice(0, 8)}-`;
    const containers = await this.docker.listContainers({ all: true });
    for (const info of containers) {
      const names = info.Names ?? [];
      if (!names.some((n) => n.replace("/", "").startsWith(prefix)))
        continue;
      try {
        await this.docker.getContainer(info.Id).remove({ force: true });
      } catch {
        continue;
      }
    }
    console.log(
      `[orchestrator] cleaned crew job_id=${jobId} status=${status}`,
    );
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

export async function main(): Promise<void> {
  const settings = settingsFromEnv();
  const orch = new Orchestrator(settings);
  await orch.start();
}
