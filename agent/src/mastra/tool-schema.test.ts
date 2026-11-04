import { expect, test } from 'bun:test';
import { inputSchema } from './tool-schema';

test('converts a real Engine search parameter list into an object schema', () => {
 expect(inputSchema([
  {name:'status',required:false,value_schema:{val_type:'string',enum:['pending','approved','denied']}},
  {name:'min_amount',required:false,value_schema:{val_type:'number'}},
  {name:'max_amount',required:false,value_schema:{val_type:'number'}},
 ])).toEqual({type:'object',properties:{status:{type:'string',enum:['pending','approved','denied']},min_amount:{type:'number'},max_amount:{type:'number'}}});
});

test('preserves required approval inputs even when Arcade can infer them',()=>{
 expect(inputSchema([
  {name:'loan_id',required:true,inferrable:true,description:'The loan',value_schema:{val_type:'string'}},
  {name:'amount',required:true,inferrable:true,value_schema:{val_type:'float'}},
 ])).toEqual({type:'object',properties:{loan_id:{type:'string',description:'The loan'},amount:{type:'number'}},required:['loan_id','amount']});
});

test('preserves arrays of nested objects and their required fields',()=>{
 expect(inputSchema([{name:'items',required:true,value_schema:{val_type:'array',inner_val_type:'json',inner_properties:{enabled:{val_type:'boolean'},count:{val_type:'integer'}},inner_required_keys:['enabled']}}])).toEqual({type:'object',properties:{items:{type:'array',items:{type:'object',properties:{enabled:{type:'boolean'},count:{type:'integer'}},required:['enabled']}}},required:['items']});
});

test('keeps an already valid JSON Schema intact',()=>{
 const schema={type:'object',properties:{loan_id:{type:'string'}},required:['loan_id']};
 expect(inputSchema(schema)).toEqual(schema);
});

test.each([[undefined],[null],[[]]])('empty input becomes a valid zero-argument tool (%p)',(raw)=>{
 expect(inputSchema(raw)).toEqual({type:'object',properties:{}});
});
