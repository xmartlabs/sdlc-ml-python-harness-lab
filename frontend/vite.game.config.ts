import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({plugins:[react()],base:'/game/',build:{outDir:'game-dist',rollupOptions:{input:{racing:resolve(process.cwd(),'gameplay.html'),cooking:resolve(process.cwd(),'cooking.html')}}}});
