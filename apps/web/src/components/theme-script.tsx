import { THEME_STORAGE_KEY } from "@/lib/theme";

/** Applies the stored theme before paint so the first render is not a light flash. */
export function ThemeScript() {
  const script = `(function(){try{var t=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});var dark=t==="dark"||(t!=="light"&&window.matchMedia("(prefers-color-scheme: dark)").matches);var d=document.documentElement;d.classList.toggle("dark",dark);d.style.colorScheme=dark?"dark":"light";}catch(e){}})();`;
  return <script dangerouslySetInnerHTML={{ __html: script }} />;
}
