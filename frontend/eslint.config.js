// ESLint 9 flat config（由 .eslintrc.cjs 等价迁移）
import { readFileSync } from 'node:fs'
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import vueTsEslintConfig from '@vue/eslint-config-typescript'
import globals from 'globals'

// unplugin-auto-import 生成的自动导入全局变量声明
const autoImportConfig = JSON.parse(
  readFileSync(new URL('./.eslintrc-auto-import.json', import.meta.url), 'utf-8')
)

export default [
  {
    ignores: ['dist', 'node_modules', 'src/types/auto-imports.d.ts', 'src/types/components.d.ts']
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  ...vueTsEslintConfig(),
  {
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node,
        ...autoImportConfig.globals
      }
    },
    rules: {
      'vue/multi-word-component-names': 'off',
      'vue/no-multiple-template-root': 'off',
      // Vue 3 + TS 下必填 prop 由类型系统约束，强制 default 反而制造假值
      'vue/require-default-prop': 'off',
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }
      ],
      'no-console': ['warn', { allow: ['warn', 'error'] }]
    }
  }
]
