export async function api(path,data,options={}) {
 const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
 const relay=()=>controller.abort();options.signal?.addEventListener('abort',relay,{once:true});
 if(options.signal?.aborted)controller.abort();
 try {
  const r=await fetch('/api/'+path,{signal:controller.signal,method:data===undefined?'GET':'POST',headers:data===undefined?{}:{'Content-Type':'application/json','X-Tokyo-Request':'1'},body:data===undefined?undefined:JSON.stringify(data)});
  let result;try{result=await r.json()}catch{throw new Error('O app respondeu de forma inesperada. Atualize a tela para conferir.');}
  if(!r.ok){const error=new Error(result.error||'Não foi possível concluir.');error.status=r.status;throw error;}return result;
 }catch(e){
  if(options.signal?.aborted)throw e;
  if(e.name==='AbortError'||e instanceof TypeError)throw new Error(data===undefined?'Não foi possível atualizar. Verifique a conexão com o app.':'Não foi possível confirmar a gravação. Atualize e confira o histórico antes de tentar novamente.');
  throw e;
 }finally{clearTimeout(timeout);options.signal?.removeEventListener('abort',relay)}
}
export const money=v=>new Intl.NumberFormat('pt-BR',{style:'currency',currency:'BRL'}).format((v||0)/100);
export const compact=v=>new Intl.NumberFormat('pt-BR',{notation:'compact',maximumFractionDigits:1}).format((v||0)/100);
export const date=v=>v?v.slice(8,10)+'/'+v.slice(5,7)+'/'+v.slice(0,4):'Sem data';
export const shortDate=v=>v?v.slice(8,10)+'/'+v.slice(5,7):'Sem data';
export const norm=v=>(v||'').normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
export const statusLabel={cancelled:'Previsão anulada',completed:'Quitado',draft:'Rascunho',accepted:'Aceito pelo fornecedor',rejected:'Recusado',negotiating:'Em negociação',forecast:'Previsto',actual:'Realizado'};
export const kindLabel={receipt:'Recebimento',obligation:'Obrigação',proposal:'Proposta',tax:'Tributo',actual:'Realizado'};
export function toCents(value){const n=Number(String(value).replace(',','.'));return Number.isFinite(n)?Math.round(n*100):0;}
export function friendlyParty(value){let name=(value||'').split(' (')[0].replace(/^\d[\d.\/ -]{5,}\s+/,'').replace(/\s+\d[\d.\/ -]{7,}$/,'').trim();return name.length>12&&name===name.toUpperCase()?name.toLowerCase().replace(/(^|[ \/])([a-zà-ÿ])/g,(m,a,b)=>a+b.toUpperCase()):name;}
