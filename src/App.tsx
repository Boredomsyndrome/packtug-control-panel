import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppProvider, useApp } from './store/AppStore'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Orders } from './pages/Orders'
import { OrderDetail } from './pages/OrderDetail'
import { Goods } from './pages/Goods'
import { Packing } from './pages/Packing'
import { Missions } from './pages/Missions'
import { Fleet } from './pages/Fleet'
import { Zones } from './pages/Zones'
import { Commands } from './pages/Commands'
import { Integration } from './pages/Integration'

function Shell() {
  const { authenticated } = useApp()
  if (!authenticated) return <Login />
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Dashboard />} />
        <Route path="/orders" element={<Orders />} />
        <Route path="/orders/:id" element={<OrderDetail />} />
        <Route path="/goods" element={<Goods />} />
        <Route path="/packing" element={<Packing />} />
        <Route path="/packing/:orderId" element={<Packing />} />
        <Route path="/missions" element={<Missions />} />
        <Route path="/fleet" element={<Fleet />} />
        <Route path="/zones" element={<Zones />} />
        <Route path="/commands" element={<Commands />} />
        <Route path="/integration" element={<Integration />} />
        <Route path="*" element={<Dashboard />} />
      </Route>
    </Routes>
  )
}

export function App() {
  return (
    <AppProvider>
      <BrowserRouter>
        <Shell />
      </BrowserRouter>
    </AppProvider>
  )
}
