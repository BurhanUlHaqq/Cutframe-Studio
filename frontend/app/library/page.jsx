import { Suspense } from "react";
import RenderLibrary from "./render-library";

export const metadata = { title: "Library | Cutframe Studio" };

export default function Page() {
  return (
    <Suspense fallback={<p className="muted" role="status">Loading library…</p>}>
      <RenderLibrary />
    </Suspense>
  );
}
