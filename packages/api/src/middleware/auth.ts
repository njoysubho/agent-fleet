import type { FastifyRequest, FastifyReply } from "fastify";

export async function verifyApiKey(
  req: FastifyRequest,
  reply: FastifyReply,
): Promise<void> {
  const expected = process.env.API_SECRET_KEY;
  if (!expected) {
    reply.code(500).send({ detail: "API_SECRET_KEY not configured" });
    return;
  }
  const key = req.headers["x-api-key"];
  if (!key || key !== expected) {
    reply.code(401).send({ detail: "Invalid API key" });
    return;
  }
}
