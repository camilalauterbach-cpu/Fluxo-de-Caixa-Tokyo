export function eventTarget(event){
 if(event.status==='actual'||event.stage==='actual')return {type:'actual',key:event.key};
 if(event.creditor_id)return {type:'plan',id:event.creditor_id};
 if(event.title_id)return {type:'title',id:event.title_id};
 return {type:'details',key:event.key};
}
export function calendarLabel(event,creditors=[]){
 const creditor=creditors.find(c=>c.id===event.creditor_id);
 const schedule=creditor?.schedule||[];
 const row=schedule.find(p=>p.key===(event.original_key||event.key));
 if(!row)return event.label||'Compromisso';
 const count=creditor.proposal.n,sequence=row.sequence;
 const original=(row.amount_cents||0)+(row.paid_cents||0);
 const percent=creditor.total_cents?new Intl.NumberFormat('pt-BR',{maximumFractionDigits:2}).format(original/creditor.total_cents*100):null;
 const prefix=event.status!=='actual'&&row.paid_cents>0?'Saldo da ':'';
 if(count===1)return (prefix?'Saldo do ':'')+'pagamento único';
 if(sequence===1)return (prefix?'Saldo da entrada':'Entrada')+(percent?' '+percent+'%':'')+' · pagamento 1 de '+count;
 return (prefix?'Saldo do pagamento ':'Pagamento ')+sequence+' de '+count+' · saldo '+(sequence-1)+' de '+(count-1);
}
export function calendarState(event){
 if(event.status==='actual'||event.stage==='actual')return {label:event.direction==='in'?'Recebido':'Pago',locked:true};
 if(event.status==='accepted'||event.stage==='decided')return {label:'Acordo aceito / decidido',locked:false};
 if(event.stage==='programmed')return {label:'Programado',locked:false};
 return {label:event.stage==='rejected'?'Recusado':'Em estudo',locked:false};
}
