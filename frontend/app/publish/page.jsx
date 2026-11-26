import PublishStudio from "./publish-studio";

export const metadata = { title: "Publish Studio" };

export default async function PublishPage({ searchParams }) {
  const { job, clip, mode, batch } = await searchParams;
  const initialJob = typeof job === "string" ? job : "";
  const initialClip = typeof clip === "string" ? clip : "";
  const initialMode = ["bulk", "queue", "tiktok"].includes(mode) ? mode : "";
  const initialBatch = typeof batch === "string" && /^[a-f0-9-]{36}$/i.test(batch) ? batch : "";
  return <PublishStudio key={`${initialJob}:${initialClip}:${initialMode}:${initialBatch}`} initialJob={initialJob}
    initialClip={initialClip} initialMode={initialMode} initialBatch={initialBatch} />;
}
