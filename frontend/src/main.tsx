/**
 * 墨记前端 v5 入口：DSH 设计令牌（ui-theme 六个全局样式表，路径 A 直消费）。
 */
import '@dsh/ui-theme/src/styles/base.css'
import '@dsh/ui-theme/src/styles/design-platform.css'
import '@dsh/ui-theme/src/styles/scrollbar.css'
import '@dsh/ui-theme/src/styles/focus.css'
import '@dsh/ui-theme/src/styles/shiki.css'
import '@dsh/ui-theme/src/styles/corner-shape.css'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import './app.css'

document.body.toggleAttribute('data-ds-dark-theme', localStorage.getItem('moji_theme') !== 'light')
createRoot(document.getElementById('root')!).render(<App />)
