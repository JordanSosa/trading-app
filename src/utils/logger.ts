export const logFeatureStub = (feature: string) => {
  if (import.meta.env.DEV) {
    // eslint-disable-next-line no-console
    console.info(`[stub] ${feature} is not yet implemented.`);
  }
};
