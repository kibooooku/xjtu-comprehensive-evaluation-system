import { describe, expect, it } from 'vitest'
import { normalizeDrag, toViewportRect } from './coordinates'

describe('evidence coordinates', () => {
  it('normalizes a reverse drag and restores it at the original viewport', () => {
    const rect = normalizeDrag({ x: 500, y: 400 }, { x: 100, y: 100 }, 800, 600)
    expect(rect).toEqual({ x: 0.125, y: 0.166667, width: 0.5, height: 0.5 })
    const viewport = toViewportRect(rect, 800, 600)
    expect(viewport.left).toBe(100)
    expect(viewport.top).toBeCloseTo(100, 3)
    expect(viewport.width).toBe(400)
    expect(viewport.height).toBe(300)
  })

  it('preserves a normalized box across zoom levels', () => {
    const rect = normalizeDrag({ x: 100, y: 50 }, { x: 300, y: 150 }, 400, 200)
    expect(rect).toEqual({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 })
    expect(toViewportRect(rect, 800, 400)).toEqual({
      left: 200, top: 100, width: 400, height: 200,
    })
  })

  it('clamps out-of-page pointer positions', () => {
    expect(normalizeDrag({ x: -20, y: -30 }, { x: 250, y: 130 }, 200, 100))
      .toEqual({ x: 0, y: 0, width: 1, height: 1 })
    expect(() => normalizeDrag({ x: 0, y: 0 }, { x: 1, y: 1 }, 0, 100))
      .toThrow('Viewport must have positive dimensions')
  })
})
