import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import App from './App.vue'
import DeclarationCreateView from './views/DeclarationCreateView.vue'

describe('App', () => {
  it('renders the declaration page through the router', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', component: DeclarationCreateView }],
    })
    router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [router, ElementPlus] } })
    expect(wrapper.get('h1').text()).toContain('班级综合素质测评申报')
  })
})
