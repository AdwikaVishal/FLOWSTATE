import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { OriginBadge, StatusBadge, VerdictBadge } from './Badge'

describe('status semantics', () => {
  it('renders status as icon, text and tone together', () => {
    const { container } = render(<StatusBadge status="FAILED" />)

    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(container.querySelector('.badge--danger')).not.toBeNull()
    expect(container.querySelector('svg')).not.toBeNull()
    expect(screen.getByTitle('Status: FAILED')).toBeInTheDocument()
  })

  it('never relies on colour alone for verdicts', () => {
    render(<VerdictBadge verdict="INCONCLUSIVE" />)

    expect(screen.getByText('Inconclusive')).toBeInTheDocument()
    expect(screen.getByTitle('Verdict: INCONCLUSIVE')).toBeInTheDocument()
  })

  it('labels demo data explicitly and live data separately', () => {
    const { rerender } = render(<OriginBadge origin="DEMO" />)
    expect(screen.getByText('Demo')).toBeInTheDocument()
    expect(screen.getByTitle(/Seeded demonstration fixture/)).toBeInTheDocument()

    rerender(<OriginBadge origin="LIVE" />)
    expect(screen.getByText('Live')).toBeInTheDocument()
  })
})