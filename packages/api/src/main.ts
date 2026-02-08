import Fastify from "fastify";
import cors from "@fastify/cors";
import websocket from "@fastify/websocket";
import { Redis } from "ioredis";
import { buildState } from "./services/state.js";
import { jobRoutes } from "./routes/jobs.js";
import { agentRoutes } from "./routes/agents.js";
import { taskRoutes } from "./routes/tasks.js";

const app = Fastify({ logger: true });

// State
const appState = buildState();
(app as unknown as { state: typeof appState }).state = appState;

// Plugins
await app.register(cors, { origin: "*" });
await app.register(websocket);

// Routes
await app.register(jobRoutes, { prefix: "/api/v1" });
await app.register(agentRoutes, { prefix: "/api/v1" });
await app.register(taskRoutes, { prefix: "/api/v1" });

// Health
app.get("/api/v1/health", async () => {
  let redisOk = false;
  try {
    redisOk = (await appState.coordinator.redis.ping()) === "PONG";
  } catch {
    redisOk = false;
  }
  return { ok: redisOk, redis: redisOk };
});

// WebSocket for job events
app.register(async (instance) => {
  instance.get<{ Params: { job_id: string } }>(
    "/api/v1/ws/jobs/:job_id",
    { websocket: true },
    (socket, req) => {
      const apiKey = req.headers["x-api-key"];
      const expected = process.env.API_SECRET_KEY;
      if (!expected || apiKey !== expected) {
        socket.close(1008, "Unauthorized");
        return;
      }

      const { job_id: jobId } = req.params;
      const sub = new Redis(
        process.env.REDIS_URL ?? "redis://redis:6379/0",
      );
      const channel =
        appState.coordinator.keys.jobEventsChannel(jobId);

      sub.subscribe(channel).catch(() => {});
      sub.on("message", (_ch: string, data: string) => {
        try {
          socket.send(data);
        } catch {
          // socket closed
        }
      });

      socket.on("close", () => {
        sub.unsubscribe(channel).catch(() => {});
        sub.quit().catch(() => {});
      });
    },
  );
});

// Graceful shutdown
app.addHook("onClose", async () => {
  await appState.coordinator.close();
});

// Start
await app.listen({ port: 8000, host: "0.0.0.0" });
