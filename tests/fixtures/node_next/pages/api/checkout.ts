import type { NextApiRequest, NextApiResponse } from "next";

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method === "POST") {
    if (req.body.total <= 0) return res.status(400).end();
    return res.status(201).json({ ok: true });
  }
  if (req.method !== "GET") return res.status(405).end();
  return res.json({});
}
