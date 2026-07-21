import { icons } from 'lucide'

function renderIconData(data, opts = {}) {
  let svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'
  if (opts.size) svg += ` width="${opts.size}" height="${opts.size}"`
  if (opts.className) svg += ` class="${opts.className}"`
  svg += '>'
  for (const [tag, attrs] of data) {
    svg += `<${tag}`
    for (const [k, v] of Object.entries(attrs)) svg += ` ${k}="${v}"`
    svg += `></${tag}>`
  }
  svg += '</svg>'
  return svg
}

function resolveName(name) {
  if (icons[name]) return name
  const pascal = name.split('-').map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('')
  return icons[pascal] ? pascal : name
}

const cache = {}

export function ico(name, opts = {}) {
  const resolved = resolveName(name)
  const merged = { size: opts.size || 14, className: opts.className }
  const key = `${resolved}_${merged.size}_${merged.className || ''}`
  if (cache[key]) return cache[key]
  const iconData = icons[resolved]
  if (!iconData) return ''
  const svg = renderIconData(iconData, merged)
  cache[key] = svg
  return svg
}

export function initIcoIcons(root = document) {
  const els = root.querySelectorAll('[data-ico]')
  if (!els.length) return
  for (const el of els) {
    const name = el.getAttribute('data-ico')
    const resolved = resolveName(name || '')
    if (!name || !icons[resolved]) continue
    const svg = renderIconData(icons[resolved], {
      size: el.getAttribute('data-ico-size') || 14,
      className: el.getAttribute('data-ico-class') || undefined
    })
    el.outerHTML = svg
  }
}
