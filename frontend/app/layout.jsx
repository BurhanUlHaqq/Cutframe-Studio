import "./globals.css";
import { PreferencesProvider } from "./preferences";
import Shell from "./shell";

export const metadata = {
  title: { default: "Cutframe Studio", template: "%s · Cutframe Studio" },
  description: "Turn YouTube videos into short clips with thumbnails and descriptions.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <script dangerouslySetInnerHTML={{ __html: `
          try {
            const theme = localStorage.getItem("cutframe-theme");
            if (theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches)) {
              document.documentElement.dataset.theme = "dark";
            }
          } catch {}
        ` }} />
        <PreferencesProvider><Shell>{children}</Shell></PreferencesProvider>
      </body>
    </html>
  );
}
