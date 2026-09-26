import { useMemo } from 'react'
import { Background, Controls, MarkerType, Position, ReactFlow } from '@xyflow/react'
import type { Edge, Node } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { api } from '../api/client'
import type { Topology, TopologyNode } from '../api/types'
import { usePoll } from '../hooks'
import { Spinner } from './ui'

/** Fixed layout: the architecture is a known shape, not a force-directed blob. */
const POSITIONS: Record<string, { x: number; y: number }> = {
  copilot: { x: 360, y: 0 },
  governor: { x: 360, y: 80 },
  approval: { x: 360, y: 160 },
  opencenter: { x: 360, y: 240 },
  gitops: { x: 360, y: 320 },
  kubernetes: { x: 160, y: 400 },
  genestack: { x: 580, y: 400 },
  'kube-scheduler': { x: 160, y: 480 },
  openstack: { x: 580, y: 480 },
  nova: { x: 580, y: 560 },
  vfio: { x: 700, y: 640 },
  'gpu-pod': { x: 40, y: 560 },
  'cpu-pod': { x: 180, y: 560 },
  database: { x: 320, y: 560 },
  'cpu-vm': { x: 470, y: 640 },
  'gpu-vm': { x: 700, y: 720 },
  ceph: { x: 0, y: 320 },
  monitoring: { x: 20, y: 700 },
}

const GROUP_COLOR: Record<string, string> = {
  ai: 'var(--series-1)',
  human: 'var(--status-warning)',
  control: 'var(--series-2)',
  platform: 'var(--series-3)',
  scheduler: 'var(--text-secondary)',
  workload: 'var(--series-1)',
  storage: 'var(--text-secondary)',
  observability: 'var(--series-3)',
}

function nodeStyle(node: TopologyNode): React.CSSProperties {
  const accent = GROUP_COLOR[node.group] ?? 'var(--text-secondary)'
  const simulated = ['simulated', 'mock', 'unavailable', 'disabled', 'none'].includes(
    node.reality,
  )
  return {
    background: node.active ? 'var(--surface-2)' : 'var(--surface-1)',
    border: `1px solid ${node.active ? accent : 'var(--border)'}`,
    borderLeft: `3px solid ${accent}`,
    borderStyle: simulated ? 'dashed' : 'solid',
    borderRadius: 8,
    padding: '6px 10px',
    width: 170,
    color: 'var(--text-primary)',
    fontSize: 11,
    boxShadow: node.active ? `0 0 0 2px ${accent}33` : 'none',
  }
}

export default function TopologyFlow({
  deploymentId,
  height = 520,
}: {
  deploymentId?: string
  height?: number
}) {
  const { data } = usePoll<Topology>(() => api.topology(deploymentId), 10000, [deploymentId])

  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as Node[], edges: [] as Edge[] }
    const nodeList: Node[] = data.nodes.map((node) => ({
      id: node.id,
      position: POSITIONS[node.id] ?? { x: 0, y: 0 },
      data: {
        label: (
          <div className="text-left">
            <div className="flex items-center justify-between gap-1">
              <span className="font-semibold">{node.label}</span>
              <span
                className="text-[9px] uppercase"
                style={{
                  color: ['real', 'connected'].includes(node.reality)
                    ? 'var(--status-good)'
                    : 'var(--status-warning)',
                }}
              >
                {node.reality}
              </span>
            </div>
            <div className="text-[10px] text-ink-secondary">{node.detail}</div>
          </div>
        ),
      },
      style: nodeStyle(node),
      sourcePosition: Position.Bottom,
      targetPosition: Position.Top,
    }))

    const edgeList: Edge[] = data.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      label: edge.active ? edge.label : undefined,
      animated: edge.active,
      style: {
        stroke: edge.active ? 'var(--accent)' : 'var(--gridline)',
        strokeWidth: edge.active ? 2 : 1,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        color: edge.active ? 'var(--accent)' : 'var(--gridline)',
      },
    }))
    return { nodes: nodeList, edges: edgeList }
  }, [data])

  if (!data) return <Spinner label="Loading topology" />

  return (
    <div style={{ height }} className="rounded-md border border-hairline">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
      >
        <Background color="var(--gridline)" gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
