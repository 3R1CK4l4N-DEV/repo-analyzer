import { NextResponse } from "next/server";

export async function GET(req: Request, { params }: { params: { id: string } }) {
  return NextResponse.json({ id: params.id });
}

export const DELETE = async () => NextResponse.json({ ok: true });
