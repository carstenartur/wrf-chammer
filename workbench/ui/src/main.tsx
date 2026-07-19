import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { SimulationWizard } from './features/simulation/SimulationWizard';
import { SystemReadiness } from './features/system/SystemReadiness';
import './styles.css';

const root = document.getElementById('root');

if (!root) {
  throw new Error('Missing root element');
}

createRoot(root).render(
  React.createElement(
    React.Fragment,
    null,
    React.createElement(SystemReadiness),
    React.createElement(SimulationWizard),
    React.createElement(App),
  ),
);
