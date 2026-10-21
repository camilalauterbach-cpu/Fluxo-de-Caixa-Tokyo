// Compare revisions on the same server; do not replace a form being edited.
export function checkRevision(current,scenarios,id,editing=false){
 const latest=scenarios.find(s=>s.id===id);
 if(!latest)throw new Error('Cenário não encontrado. Atualize a lista de cenários.');
 if(!current||current.id!==id)return 'refresh';
 if(latest.version!==current.version)return editing?'pending':'refresh';
 return 'current';
}
