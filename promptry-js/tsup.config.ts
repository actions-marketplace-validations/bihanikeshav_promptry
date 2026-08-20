import { defineConfig } from 'tsup';

export default defineConfig({
  entry: ['src/index.ts', 'src/openai.ts'],
  format: ['esm', 'cjs'],
  dts: true,
  clean: true,
  target: 'es2020',
  minify: true,
  splitting: false,
});
