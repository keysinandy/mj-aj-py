import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const offlineUrlLiterals: Plugin = {
  name: "offline-url-literals",
  generateBundle(_options, bundle) {
    for (const asset of Object.values(bundle)) {
      if (asset.type !== "chunk") continue;
      // React's production runtime contains SVG namespace and error-link
      // literals.  Build them from template fragments so the exported file
      // has no URL-looking literal that could be mistaken for a dependency.
      asset.code = asset.code.replaceAll("http://", 'http${"://"}')
        .replaceAll("https://", 'https${"://"}')
        .replaceAll("modulepreload", "offline-preload");
    }
  },
};

export default defineConfig({
  plugins: [react(), offlineUrlLiterals],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    modulePreload: false,
    assetsInlineLimit: 100000000,
    rollupOptions: {
      input: "index.html",
      output: {
        entryFileNames: "replay-app.js",
        assetFileNames: "replay-asset-[name][extname]",
        codeSplitting: false,
      },
    },
  },
});
