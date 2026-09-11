import 'element-plus/dist/index.css'
import '@/styles/tokens.css'
import '@/styles/base.css'

import ElementPlus from 'element-plus'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { installAuthGuards, router } from './router'

const pinia = createPinia()
installAuthGuards(router, pinia)

createApp(App).use(pinia).use(router).use(ElementPlus).mount('#app')
