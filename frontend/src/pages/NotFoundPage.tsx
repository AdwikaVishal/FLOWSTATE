import { Link } from 'react-router-dom'
import { Icon } from '../components/Icon'
import { buttonClass } from '../components/ui/Button'
import { Card, CardBody } from '../components/ui/Card'
import { EmptyState } from '../components/ui/States'

export function NotFoundPage() {
  return (
    <Card>
      <CardBody>
        <EmptyState
          icon="search"
          title="Console route not found"
          description="That screen does not exist. Use the navigation to return to a known view."
          action={
            <Link to="/" className={buttonClass('primary', 'md')}>
              <Icon name="arrowLeft" size={15} />
              <span>Back to overview</span>
            </Link>
          }
        />
      </CardBody>
    </Card>
  )
}
