import type { ResourcePlan } from './api/types'

/** Mirrors `ResourcePlan.summary()` on the backend. */
export function planSummary(plan: ResourcePlan): string {
  const bits = [
    `${plan.replicas}x`,
    `${plan.vcpu_per_replica} vCPU`,
    `${plan.ram_gb_per_replica} GB RAM`,
  ]
  if (plan.gpu_per_replica > 0) {
    bits.push(`${plan.gpu_per_replica}x ${plan.gpu_model ?? 'GPU'}`)
  }
  bits.push(`${plan.storage_gb} GB ${plan.storage_backend}`)
  return bits.join(', ')
}

export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const seconds = Math.round((Date.now() - then) / 1000)
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return new Date(iso).toLocaleDateString()
}

export function clock(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleTimeString()
}

export function titleCase(value: string): string {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}
