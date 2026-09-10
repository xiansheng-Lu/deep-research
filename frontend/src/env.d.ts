/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE: string
  readonly VITE_MOCK: 'gateway' | 'off'
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  // Vue 3.5 的 DefineComponent 泛型均有默认值，无需旧式空对象占位
  const component: DefineComponent
  export default component
}
