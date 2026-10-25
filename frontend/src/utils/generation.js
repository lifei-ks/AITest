export const LIVE_GENERATION_ID = '__live__'

export function generationLabel(g) {
  const time = g.createdAt ? new Date(g.createdAt).toLocaleString() : '未知时间'
  const summary = g.summary || {}
  const llm = Number(summary.llmCaseCount || 0)
  const rule = Number(summary.ruleCaseCount || 0)
  let modeText = '规则'
  if (g.mode === 'legacy') {
    modeText = llm && rule ? `历史归档（LLM ${llm} + 规则 ${rule}）` : '历史归档'
  } else if (g.mode === 'incremental') {
    modeText = '增量生成'
  } else if (g.mode === 'llm') {
    modeText = llm && rule ? `LLM ${llm} + 规则 ${rule}` : 'LLM'
  } else if (g.mode === 'rule') {
    modeText = rule ? `规则 ${rule}` : '规则'
  } else if (g.mode) {
    modeText = g.mode
  }
  return `${time} · ${modeText} · ${g.caseCount} 条`
}

export function generationOptionPrefix(g) {
  if (g.mode === 'legacy') return '归档'
  return g.isLatest ? '最新' : '历史'
}
