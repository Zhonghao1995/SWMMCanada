import ControlPanel from './components/ControlPanel'
import MapPanel from './components/MapPanel'

export default function App() {
  return (
    <div className="flex h-full w-full">
      <ControlPanel />
      <main className="relative h-full flex-1">
        <MapPanel />
      </main>
    </div>
  )
}
