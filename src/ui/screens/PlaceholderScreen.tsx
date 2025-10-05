interface PlaceholderScreenProps {
  label: string;
}

export const PlaceholderScreen = ({ label }: PlaceholderScreenProps) => {
  return (
    <div className="rounded-lg border border-dashed border-slate-700 bg-slate-900/60 p-6 text-center text-slate-400">
      <h2 className="text-lg font-semibold text-ember-300">{label}</h2>
      <p className="mt-2 text-sm">
        This section is scaffolded and ready for the upcoming gameplay systems.
      </p>
    </div>
  );
};
