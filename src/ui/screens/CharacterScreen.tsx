export const CharacterScreen = () => {
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <section className="rounded-lg border border-slate-800 bg-slate-900/80 p-4 shadow-lg">
        <h2 className="text-xl font-semibold text-ember-300">Hero Sheet</h2>
        <p className="mt-2 text-sm text-slate-300">
          Detailed hero stats, resistances, and derived values will render here.
        </p>
      </section>
      <section className="rounded-lg border border-slate-800 bg-slate-900/80 p-4 shadow-lg">
        <h2 className="text-xl font-semibold text-ember-300">Talents & Skills</h2>
        <p className="mt-2 text-sm text-slate-300">
          Allocate points, preview capstones, and customize your archetype when these systems are ready.
        </p>
      </section>
    </div>
  );
};
