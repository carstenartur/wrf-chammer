import React from 'react';
import type { WorkbenchEvent } from '../../shared/api/types';

type Props = {
  query: string;
  events: WorkbenchEvent[];
  onQueryChange: (query: string) => void;
  onSearch: () => void;
  onSelect: (eventId: string) => void;
};

export function EventSearch(props: Props) {
  const cards = props.events.length === 0
    ? React.createElement('div', { className: 'empty-state' }, 'No matching events found.')
    : props.events.map((item) => React.createElement(
        'article',
        { className: 'event-card', key: item.id },
        React.createElement('h3', null, item.name || item.id),
        React.createElement('p', null, item.description || 'No description available.'),
        React.createElement('button', { type: 'button', onClick: () => props.onSelect(item.id) }, `Select ${item.id}`),
      ));

  return React.createElement(
    'section',
    { className: 'panel search-panel', 'aria-labelledby': 'event-search-title' },
    React.createElement('h2', { id: 'event-search-title' }, '1. Search event'),
    React.createElement(
      'form',
      {
        className: 'search-form',
        onSubmit: (evt: React.FormEvent) => {
          evt.preventDefault();
          props.onSearch();
        },
      },
      React.createElement('label', { htmlFor: 'event-query' }, 'Event name'),
      React.createElement(
        'div',
        { className: 'inline-form' },
        React.createElement('input', {
          id: 'event-query',
          name: 'query',
          value: props.query,
          autoComplete: 'off',
          onChange: (evt: React.ChangeEvent<HTMLInputElement>) => props.onQueryChange(evt.currentTarget.value),
        }),
        React.createElement('button', { type: 'submit' }, 'Search'),
      ),
    ),
    React.createElement('div', { id: 'event-results', className: 'results', 'aria-live': 'polite' }, cards),
  );
}
