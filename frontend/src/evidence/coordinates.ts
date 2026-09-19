export interface Point {
  x: number
  y: number
}

export interface NormalizedRect {
  x: number
  y: number
  width: number
  height: number
}

export interface ViewportRect {
  left: number
  top: number
  width: number
  height: number
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

function six(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000
}

export function normalizeDrag(
  start: Point,
  end: Point,
  viewportWidth: number,
  viewportHeight: number,
): NormalizedRect {
  if (viewportWidth <= 0 || viewportHeight <= 0) {
    throw new Error('Viewport must have positive dimensions')
  }
  const left = clamp(Math.min(start.x, end.x), 0, viewportWidth)
  const top = clamp(Math.min(start.y, end.y), 0, viewportHeight)
  const right = clamp(Math.max(start.x, end.x), 0, viewportWidth)
  const bottom = clamp(Math.max(start.y, end.y), 0, viewportHeight)
  const x = six(left / viewportWidth)
  const y = six(top / viewportHeight)
  return {
    x,
    y,
    width: six(right / viewportWidth) - x,
    height: six(bottom / viewportHeight) - y,
  }
}

export function toViewportRect(
  rect: NormalizedRect,
  viewportWidth: number,
  viewportHeight: number,
): ViewportRect {
  return {
    left: rect.x * viewportWidth,
    top: rect.y * viewportHeight,
    width: rect.width * viewportWidth,
    height: rect.height * viewportHeight,
  }
}
