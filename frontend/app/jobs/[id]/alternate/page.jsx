import { notFound } from "next/navigation";
import RenderStudio from "../../../render-studio";

export const metadata = { title: "Reuse render source" };

export default async function AlternateRenderPage({ params }) {
  const { id } = await params;
  if (!/^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i.test(id)) notFound();
  return <RenderStudio key={id} reuseId={id} />;
}
