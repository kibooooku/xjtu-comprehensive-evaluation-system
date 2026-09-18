import { createRouter, createWebHistory } from 'vue-router'

import DeclarationCreateView from '@/views/DeclarationCreateView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: '/declarations/new' },
    { path: '/declarations/new', name: 'declaration-create', component: DeclarationCreateView },
  ],
})

export default router
