import { useMemo } from 'react';
import { Link, Outlet, Route, Routes } from 'react-router-dom';
import { AppProviders } from './app/providers';
import { RunScreen } from './ui/screens/RunScreen';
import { CharacterScreen } from './ui/screens/CharacterScreen';
import { PlaceholderScreen } from './ui/screens/PlaceholderScreen';

const ShellLayout = () => {
  const navLinks = useMemo(
    () => [
      { label: 'Run', path: '/' },
      { label: 'Character', path: '/character' },
      { label: 'Inventory', path: '/inventory' },
      { label: 'Crafting', path: '/crafting' },
      { label: 'Zones', path: '/zones' },
      { label: 'Settings', path: '/settings' }
    ],
    []
  );

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-6">
        <header className="flex flex-col gap-3 border-b border-ember-500 pb-4">
          <h1 className="text-3xl font-bold text-ember-400">Endless Embers</h1>
          <nav className="flex flex-wrap gap-2">
            {navLinks.map((link) => (
              <Link
                key={link.path}
                to={link.path}
                className="rounded bg-slate-900 px-3 py-2 text-sm font-semibold text-slate-200 transition hover:bg-ember-600 hover:text-slate-50"
              >
                {link.label}
              </Link>
            ))}
          </nav>
        </header>
        <main className="flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

const AppRouter = () => (
  <Routes>
    <Route element={<ShellLayout />}>
      <Route path="/" element={<RunScreen />} />
      <Route path="/character" element={<CharacterScreen />} />
      <Route path="/inventory" element={<PlaceholderScreen label="Inventory" />} />
      <Route path="/crafting" element={<PlaceholderScreen label="Crafting" />} />
      <Route path="/zones" element={<PlaceholderScreen label="Zones" />} />
      <Route path="/settings" element={<PlaceholderScreen label="Settings" />} />
    </Route>
  </Routes>
);

function App() {
  return (
    <AppProviders>
      <AppRouter />
    </AppProviders>
  );
}

export default App;
