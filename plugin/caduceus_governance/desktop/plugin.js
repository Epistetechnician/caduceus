// Caduceus governance: a read-only pane and a status-bar chip. Releasing a held change is deliberately CLI-only (`caduceus release-effect`).
import { host, useQuery } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState } from 'react'

const REFRESH_MS = 5000

function useStatus(ctx) {
  return useQuery({ queryKey: [ctx.source, 'status'], queryFn: () => ctx.rest('/status'), refetchInterval: REFRESH_MS })
}

function heldCount(data) {
  if (!data || !data.surfaces) return 0
  return Object.values(data.surfaces).reduce((n, s) => n + ((s && s.held_effects) || []).length, 0)
}

function Row({ label, children }) {
  return jsxs('div', { className: 'flex justify-between gap-2', children: [
    jsx('span', { className: 'text-(--ui-text-tertiary)', children: label }),
    jsx('span', { className: 'font-mono text-xs', children })
  ] })
}

function Held({ ctx, surface, digest }) {
  const [open, setOpen] = useState(false)
  const review = useQuery({ queryKey: [ctx.source, 'review', surface, digest], queryFn: () => ctx.rest('/review/' + surface + '/' + digest), enabled: open })
  const files = (review.data && review.data.files) || []
  return jsxs('div', { className: 'rounded border p-2 text-xs', children: [
    jsxs('button', { type: 'button', className: 'font-mono', onClick: () => setOpen(!open), children: [(open ? '▾ ' : '▸ ') + surface + ' ' + digest.slice(0, 12)] }),
    open && review.data && jsx('div', { className: 'mt-1 text-(--ui-text-tertiary)', children: (review.data.reasons || []).join(', ') + (review.data.taint_tools && review.data.taint_tools.length ? ' · after ' + review.data.taint_tools.join(', ') : '') }),
    open && files.map(f => jsxs('div', { key: f.path, className: 'mt-1', children: [
      jsx('div', { className: 'font-medium', children: f.status + ' ' + f.path }),
      jsx('pre', { className: 'max-h-40 overflow-auto whitespace-pre-wrap font-mono', children: f.diff.join('\n') })
    ] })),
    open && review.data && jsx('div', { className: 'mt-1 font-mono text-(--ui-text-tertiary)', children: review.data.release })
  ] })
}

function Pane({ ctx }) {
  const { data, isLoading, error } = useStatus(ctx)
  if (isLoading) return jsx('div', { className: 'p-3 text-sm', children: 'Caduceus: loading…' })
  if (error || !data) return jsx('div', { className: 'p-3 text-sm', children: 'Caduceus: status unavailable' })
  const surfaces = Object.entries(data.surfaces || {})
  return jsxs('div', { className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm', children: [
    jsx('div', { className: 'font-medium', children: 'Caduceus · mode ' + data.mode }),
    surfaces.map(([name, s]) => jsxs('div', { key: name, className: 'flex flex-col gap-1', children: [
      jsx('div', { className: 'font-medium', children: name }),
      s.error ? jsx('div', { children: 'error: ' + s.error }) : jsxs('div', { className: 'flex flex-col gap-1', children: [
        jsx(Row, { label: 'head', children: (s.head || 'none').slice(0, 12) }),
        jsx(Row, { label: 'frozen', children: String(s.frozen) }),
        jsx(Row, { label: 'held', children: String((s.held_effects || []).length) }),
        (s.held_effects || []).map(d => jsx(Held, { key: d, ctx, surface: name, digest: d })),
        (s.last_verdicts || []).slice(-3).map((v, i) => jsx('div', { key: i, className: 'font-mono text-xs text-(--ui-text-tertiary)', children: v.decision + ' ' + v.reasons.join(',') }))
      ] })
    ] }))
  ] })
}

function Chip({ ctx }) {
  const { data } = useStatus(ctx)
  const n = heldCount(data)
  return jsx('span', { className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)', title: 'Caduceus: held changes', children: 'caduceus ' + (n ? n + ' held' : 'ok') })
}

export default {
  id: 'caduceus-governance',
  name: 'Caduceus governance',
  register(ctx) {
    ctx.register({ id: 'pane', area: 'panes', title: 'caduceus', data: { placement: 'right', width: '320px' }, render: () => jsx(Pane, { ctx }) })
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 120,
      render: () => jsx(Chip, { ctx })
    })
  }
}
