import { useMemo, useRef, useState } from 'react'

export interface TrendPoint {
  ts: number // epoch ms
  value: number
}

interface Props {
  title: string
  points: TrendPoint[]
  unit: string
  color: string
}

const W = 600
const H = 130
const PAD = { top: 10, right: 12, bottom: 22, left: 44 }

export default function TrendChart({ title, points, unit, color }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [hover, setHover] = useState<TrendPoint | null>(null)

  const { path, xFor, yFor, lo, hi } = useMemo(() => {
    const values = points.map((p) => p.value)
    const lo = Math.min(...values)
    const hi = Math.max(...values)
    const span = hi - lo || 1
    const t0 = points[0]?.ts ?? 0
    const t1 = points[points.length - 1]?.ts ?? 1
    const tSpan = t1 - t0 || 1
    const xFor = (ts: number) => PAD.left + ((ts - t0) / tSpan) * (W - PAD.left - PAD.right)
    const yFor = (v: number) => PAD.top + (1 - (v - lo) / span) * (H - PAD.top - PAD.bottom)
    const path = points
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${xFor(p.ts).toFixed(1)},${yFor(p.value).toFixed(1)}`)
      .join(' ')
    return { path, xFor, yFor, lo, hi }
  }, [points])

  if (points.length < 2) {
    return (
      <div className="trend-chart">
        <h4>{title}</h4>
        <p className="muted">Not enough readings yet.</p>
      </div>
    )
  }

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const x = ((event.clientX - rect.left) / rect.width) * W
    let nearest = points[0]
    for (const p of points) {
      if (Math.abs(xFor(p.ts) - x) < Math.abs(xFor(nearest.ts) - x)) nearest = p
    }
    setHover(nearest)
  }

  return (
    <div className="trend-chart">
      <h4>{title}</h4>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
        role="img"
        aria-label={`${title}: ${points.length} readings between ${lo.toFixed(1)} and ${hi.toFixed(1)} ${unit}`}
      >
        <line x1={PAD.left} y1={yFor(lo)} x2={W - PAD.right} y2={yFor(lo)} className="grid" />
        <line x1={PAD.left} y1={yFor(hi)} x2={W - PAD.right} y2={yFor(hi)} className="grid" />
        <text x={PAD.left - 6} y={yFor(hi) + 4} className="axis" textAnchor="end">
          {hi.toFixed(1)}
        </text>
        <text x={PAD.left - 6} y={yFor(lo) + 4} className="axis" textAnchor="end">
          {lo.toFixed(1)}
        </text>
        <text x={PAD.left} y={H - 6} className="axis">
          {new Date(points[0].ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </text>
        <text x={W - PAD.right} y={H - 6} className="axis" textAnchor="end">
          {new Date(points[points.length - 1].ts).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </text>
        <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
        {hover && (
          <g>
            <line
              x1={xFor(hover.ts)}
              y1={PAD.top}
              x2={xFor(hover.ts)}
              y2={H - PAD.bottom}
              className="crosshair"
            />
            <circle cx={xFor(hover.ts)} cy={yFor(hover.value)} r={4} fill={color} stroke="#fff" strokeWidth={2} />
          </g>
        )}
      </svg>
      <div className="trend-readout">
        {hover
          ? `${new Date(hover.ts).toLocaleString()} — ${hover.value.toFixed(1)} ${unit}`
          : `latest ${points[points.length - 1].value.toFixed(1)} ${unit}`}
      </div>
    </div>
  )
}
