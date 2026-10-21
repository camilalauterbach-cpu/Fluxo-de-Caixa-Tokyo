import test from 'node:test';
import assert from 'node:assert/strict';
import {matchesSupplier,suppliersCsv,printSuppliers} from '../web/supplierContacts.js';

const suppliers=[
 {id:'C-01',name:'Razão Social Alfa',trade_name:'Café Horizonte',negotiation_contact:'Márcia Silva',negotiation_phone:'+55 (11) 98765-4321',proposal:{status:'negotiating'}},
 {id:'C-02',name:'Fornecedor Beta',proposal:{status:'accepted'}},
];

test('search finds legal name, nickname, contact, phone and ID without accent/case sensitivity',()=>{
 for(const query of ['razao social','CAFE HORIZONTE','marcia','98765','5511987654321','c-01'])assert.equal(matchesSupplier(suppliers[0],query),true,query);
 assert.equal(matchesSupplier(suppliers[1],'marcia'),false);
 assert.equal(matchesSupplier(suppliers[1],'   '),true);
});

test('export includes exactly the filtered supplier list and the optional contact columns',()=>{
 const filtered=suppliers.filter(supplier=>matchesSupplier(supplier,'horizonte'));
 const csv=suppliersCsv(filtered);
 assert.ok(csv.startsWith('\uFEFF"Código";"Fornecedor";"Nome fantasia";"Contato da negociação";"Telefone";"Situação"\r\n'));
 assert.ok(csv.includes('"C-01";"Razão Social Alfa";"Café Horizonte";"Márcia Silva";"\'+55 (11) 98765-4321";"Em negociação"\r\n'));
 assert.equal(csv.includes('Fornecedor Beta'),false);
});

test('CSV preserves quoted semicolons, double quotes and line breaks as one field',()=>{
 const csv=suppliersCsv([{id:'X',name:'Loja; "Horizonte"\nFilial',negotiation_contact:'Ana\r\nSilva',proposal:{status:'draft'}}]);
 assert.ok(csv.includes('"Loja; ""Horizonte""\nFilial"'));
 assert.ok(csv.includes('"Ana\r\nSilva"'));
});

test('CSV neutralizes formula prefixes including leading spaces, tabs and newlines',()=>{
 for(const value of ['=2+2','+SUM(1;2)','-2+3','@SUM(A1)',' \t=HYPERLINK("url")','\n+123','\t-42']){
  const csv=suppliersCsv([{id:'X',name:value,proposal:{status:'draft'}}]);
  const quoted='"\''+value.replaceAll('"','""')+'"';
  assert.ok(csv.includes(quoted),JSON.stringify(value));
 }
});

test('legacy missing contacts stay empty and fully-paid status takes precedence',()=>{
 const csv=suppliersCsv([{id:'C-02',name:'Fornecedor Beta',fully_paid:true,proposal:{status:'accepted'}}]);
 assert.ok(csv.endsWith('"C-02";"Fornecedor Beta";"";"";"";"Quitado"\r\n'));
 assert.equal(csv.includes('undefined'),false);
 assert.equal(csv.includes('null'),false);
});

test('empty filtered list exports only headers',()=>{
 assert.equal(suppliersCsv([]).split('\r\n').length,2);
});

test('print reports a blocked popup without trying to open a document',()=>{
 const old=globalThis.window;
 globalThis.window={open:()=>null};
 try{assert.equal(printSuppliers(suppliers),false)}finally{if(old===undefined)delete globalThis.window;else globalThis.window=old}
});
