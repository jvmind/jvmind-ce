import { defineConfig } from "vite";
import { resolve } from "path";

function injectCssLink() {
  return {
    name: "inject-css-link",
    transformIndexHtml(html) {
      if (html.includes("<!-- inject-css-link -->")) {
        return html.replace(
          "<!-- inject-css-link -->",
          '<link rel="stylesheet" href="/src/style.css">'
        );
      }
      return html;
    },
  };
}

export default defineConfig({
  root: ".",
  base: "/",
  plugins: [injectCssLink()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    rollupOptions: {
      input: {
        main: resolve(__dirname, "index.html"),
        report: resolve(__dirname, "report.html"),
      },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});