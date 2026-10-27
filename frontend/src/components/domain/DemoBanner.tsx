import { Link } from 'react-router-dom'
import { useDemo } from '../../api/queries'
import { Icon } from '../Icon'
import { Badge } from '../ui/Badge'

export function DemoBanner() {
  const demo = useDemo()
  if (!demo.data?.seeded) return null

  return (
    <div className="demo-banner">
      <span className="demo-banner__icon">
        <Icon name="flask" size={16} />
      </span>
      <div className="demo-banner__text">
        <strong>Demonstration workspace</strong>
        <span>
          Invoice Approval and its five sample runs are seeded fixtures, not activity produced
          by this installation.
        </span>
      </div>
      <Badge tone="accent" mono>
        {demo.data.batch}
      </Badge>
      <Link to="/settings" className="demo-banner__link">
        Manage fixtures
        <Icon name="arrowRight" size={13} />
      </Link>
    </div>
  )
}