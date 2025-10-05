import { create } from 'zustand';

export interface GameState {
  schemaVersion: number;
  heroName: string;
}

export const useGameStore = create<GameState>(() => ({
  schemaVersion: 1,
  heroName: 'Unnamed Hero'
}));
