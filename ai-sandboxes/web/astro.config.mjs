// Agisphire marketing site: Astro static build.
import { defineConfig } from 'astro/config';

export default defineConfig({
  site: 'https://agisphire.example',
  output: 'static',
  build: {
    // Keep CSS inline in the HTML for single-file pages (fewer requests, simpler hosting).
    inlineStylesheets: 'always',
  },
});
