import {norm,statusLabel} from './api.js';

const columns=['Código','Fornecedor','Nome fantasia','Contato da negociação','Telefone','Situação'];
const asText=value=>String(value??'');

export function matchesSupplier(creditor,search){
 const query=norm(asText(search).trim());
 if(!query)return true;
 const values=[creditor.id,creditor.name,creditor.trade_name,creditor.negotiation_contact,creditor.negotiation_phone];
 if(values.some(value=>norm(asText(value)).includes(query)))return true;
 const digits=query.replace(/\D/g,'');
 return !!digits&&/^[+\d\s().-]+$/.test(query)&&asText(creditor.negotiation_phone).replace(/\D/g,'').includes(digits);
}

function rowsFor(creditors){
 return creditors.map(creditor=>[
  creditor.id,creditor.name,creditor.trade_name,creditor.negotiation_contact,creditor.negotiation_phone,
  creditor.fully_paid?statusLabel.completed:statusLabel[creditor.proposal?.status]||creditor.proposal?.status||'Não informado',
 ].map(asText));
}

function csvCell(value){
 const text=asText(value);
 const safe=/^\s*[=+\-@]/.test(text)?"'"+text:text;
 return '"'+safe.replaceAll('"','""')+'"';
}

export function suppliersCsv(creditors){
 return '\uFEFF'+[columns,...rowsFor(creditors)].map(row=>row.map(csvCell).join(';')).join('\r\n')+'\r\n';
}

export function downloadSuppliersCsv(creditors){
 const url=URL.createObjectURL(new Blob([suppliersCsv(creditors)],{type:'text/csv;charset=utf-8'}));
 const link=document.createElement('a');
 link.href=url;
 link.download='fornecedores-negociacao.csv';
 link.hidden=true;
 document.body.append(link);
 try{link.click()}finally{link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
}

export function printSuppliers(creditors){
 const popup=window.open('','_blank','width=1100,height=800');
 if(!popup)return false;
 popup.opener=null;
 const doc=popup.document;
 doc.documentElement.lang='pt-BR';
 doc.head.replaceChildren();
 doc.body.replaceChildren();
 const charset=doc.createElement('meta');charset.setAttribute('charset','utf-8');
 const title=doc.createElement('title');title.textContent='Fornecedores para negociação';
 const style=doc.createElement('style');
 style.textContent='body{font:14px Arial,sans-serif;color:#182524;margin:24px}h1{font-size:24px;margin-bottom:8px}p{margin:0 0 20px}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #bac8c0;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#edf3ef}thead{display:table-header-group}tr{break-inside:avoid}@page{size:landscape;margin:12mm}';
 doc.head.append(charset,title,style);
 const heading=doc.createElement('h1');heading.textContent='Fornecedores para negociação';
 const count=doc.createElement('p');count.textContent=creditors.length+' fornecedor(es) na lista filtrada.';
 const table=doc.createElement('table');
 const head=doc.createElement('thead');const header=doc.createElement('tr');
 for(const label of columns){const cell=doc.createElement('th');cell.scope='col';cell.textContent=label;header.append(cell)}
 head.append(header);table.append(head);
 const body=doc.createElement('tbody');
 for(const row of rowsFor(creditors)){
  const tr=doc.createElement('tr');
  for(const value of row){const cell=doc.createElement('td');cell.textContent=value;tr.append(cell)}
  body.append(tr);
 }
 table.append(body);doc.body.append(heading,count,table);
 const requestPrint=()=>{if(!popup.closed){popup.focus();popup.print()}};
 if(doc.readyState==='loading')doc.addEventListener('DOMContentLoaded',requestPrint,{once:true});
 else requestPrint();
 return true;
}
