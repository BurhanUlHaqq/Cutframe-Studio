import RenderDetails from "./render-details";

export const metadata = { title: "Render details" };

export default async function RenderPage({ params }) {
  const { id } = await params;
  return <RenderDetails id={id} />;
}
