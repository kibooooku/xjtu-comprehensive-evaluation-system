import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import App from './App.vue'
import HomeView from './views/HomeView.vue'

describe('App', () => {
  it('renders the bootstrap home page through the router', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: HomeView }],
    })
    router.push('/')
    await router.isReady()

    const wrapper = mount(App, {
      global: { plugins: [router, ElementPlus] },
    })

    expect(wrapper.get('h1').text()).toContain('综合素质测评管理系统')
  })
})
