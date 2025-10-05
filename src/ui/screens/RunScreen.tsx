export const RunScreen = () => {
  return (
    <div className="grid gap-6 md:grid-cols-[2fr,1fr]">
      <section className="rounded-lg border border-slate-800 bg-slate-900/80 p-4 shadow-lg">
        <h2 className="text-xl font-semibold text-ember-300">Combat Log</h2>
        <p className="mt-2 text-sm text-slate-300">
          The combat simulation and event feed will appear here once the core loop is implemented.
        </p>
      </section>
      <aside className="rounded-lg border border-slate-800 bg-slate-900/80 p-4 shadow-lg">
        <h2 className="text-xl font-semibold text-ember-300">Hero Overview</h2>
        <p className="mt-2 text-sm text-slate-300">
          Track hero stats, active buffs, and current zone progress from this panel.
        </p>
      </aside>
      <section className="md:col-span-2 rounded-lg border border-slate-800 bg-slate-900/80 p-4 shadow-lg">
        <h2 className="text-xl font-semibold text-ember-300">Loot Ribbon</h2>
        <p className="mt-2 text-sm text-slate-300">
          Recent drops and auto-salvage feedback will flow along this ribbon during runs.
        </p>
      </section>
    </div>
  );
};
