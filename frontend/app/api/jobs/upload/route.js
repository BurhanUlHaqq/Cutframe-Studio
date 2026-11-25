export const runtime = "nodejs";

export async function POST(request) {
  const headers = new Headers();
  for (const name of ["content-type", "content-length", "cookie", "x-csrftoken", "origin", "referer"]) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const backend = process.env.DJANGO_API_URL || "http://127.0.0.1:5032";
  try {
    const response = await fetch(`${backend}/api/jobs/upload/`, {
      method: "POST", headers, body: request.body, duplex: "half",
      signal: request.signal, cache: "no-store", redirect: "manual",
    });
    const outgoing = new Headers({ "cache-control": "no-store" });
    const contentType = response.headers.get("content-type");
    if (contentType) outgoing.set("content-type", contentType);
    for (const cookie of response.headers.getSetCookie()) outgoing.append("set-cookie", cookie);
    return new Response(response.body, { status: response.status, headers: outgoing });
  } catch {
    return Response.json({ error: "Cutframe could not transfer the video to the render server." }, { status: 502 });
  }
}
