import React from 'react';
import logo from './assets/tokyo-eventos.png';

export default function TokyoBrand({className=''}) {
 return <span className={'tokyo-brand '+className}>
  <span className="tokyo-brand-mark"><img className="tokyo-brand-logo" src={logo} width="185" height="186" alt="Tokyo Eventos" decoding="async"/></span>
  <span className="tokyo-brand-caption">Caixa e negociação</span>
 </span>;
}
