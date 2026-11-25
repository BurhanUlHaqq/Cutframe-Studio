import PublishStudio from "./publish-studio";

export const metadata = { title: "Publish Studio" };

export default async function PublishPage({ searchParams }) {
  const { job, clip } = await searchParams;
  return <PublishStudio initialJob={typeof job === "string" ? job : ""}
    initialClip={typeof clip === "string" ? clip : ""} />;
}
