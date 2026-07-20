import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { Era5DataPanel } from './features/data/Era5DataPanel';
import { JobQueuePanel } from './features/jobs/JobQueuePanel';
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
    React.createElement(Era5DataPanel),
    React.createElement(JobQueuePanel),
    React.createElement(App),
  ),
);
