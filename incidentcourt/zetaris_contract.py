"""Strict decoding of Zetaris's documented SQL result envelope.

Official example: https://kbase.zetaris.com/knowledge/pipeline-scheduling
uses DataFrame(result['data'], columns=result['headers']). The official client
setup says REST numeric values are strings, including exponential decimals:
https://github.com/zetaris/getting-started/blob/main/docs/connections/client-setup.md

These contracts still need matching against the user's actual instance. This
module validates responses; it never treats a missing envelope as zero rows.
"""
from __future__ import annotations
from decimal import Decimal, DecimalException, InvalidOperation
import math

INTEGER_COLUMNS = frozenset({
    'one','connected','observations','spans','baseline_n','current_n','status',
    'count','count(*)','n','row_count',
})
NUMBER_COLUMNS = frozenset({
    'start_ts','end_ts','ts','value','baseline','current','baseline_square',
    'baseline_ms','current_ms','first_ts','last_ts','child_mean_ms','duration_ms',
})
ID_COLUMNS = frozenset({'id','incident_id','span_id','parent_id','trace_id'})
MAX_ROWS = 1000
MAX_COLUMNS = 100


class ZetarisContractError(ValueError):
    """A sanitized response validation failure; never includes remote content."""


def normalize_row(row: dict) -> dict:
    result={}
    for column,value in row.items():
        key=column.lower()
        if value is None:
            result[column]=None
            continue
        if key in INTEGER_COLUMNS or key in NUMBER_COLUMNS:
            if isinstance(value,bool) or not isinstance(value,(str,int,float)):
                raise ZetarisContractError('Zetaris returned an invalid numeric telemetry value.')
            try:
                numeric=Decimal(str(value).strip())
                if not numeric.is_finite():
                    raise InvalidOperation()
                if key in INTEGER_COLUMNS:
                    if numeric.copy_abs()>Decimal('1e18') or numeric!=numeric.to_integral_value():
                        raise InvalidOperation()
                    result[column]=int(numeric)
                else:
                    number=float(numeric)
                    if not math.isfinite(number):
                        raise InvalidOperation()
                    result[column]=number
            except (DecimalException,ValueError,OverflowError):
                raise ZetarisContractError('Zetaris returned an invalid numeric telemetry value.') from None
        elif key in ID_COLUMNS:
            if not isinstance(value,str):
                raise ZetarisContractError('Zetaris returned a non-string telemetry identifier.')
            result[column]=value
        else:
            if isinstance(value,(dict,list)):
                raise ZetarisContractError('Zetaris returned a nested value for a flat telemetry column.')
            result[column]=value
    return result


def decode_rows(payload) -> list[dict]:
    """Decode only the documented headers/data envelope, capped at1000 rows."""
    if not isinstance(payload,dict):
        raise ZetarisContractError('Zetaris SQL result must contain headers and data.')
    if payload.get('error') or payload.get('errors') or payload.get('success') is False:
        raise ZetarisContractError('Zetaris reported a SQL execution error.')
    if str(payload.get('status','')).lower() in ('error','failed','failure'):
        raise ZetarisContractError('Zetaris reported a SQL execution error.')
    headers,data=payload.get('headers'),payload.get('data')
    if (not isinstance(headers,list) or not 1<=len(headers)<=MAX_COLUMNS
            or any(not isinstance(h,str) or not h or len(h)>200 for h in headers)
            or len({h.lower() for h in headers})!=len(headers)
            or not isinstance(data,list)):
        raise ZetarisContractError('Zetaris SQL result headers or rows are invalid.')
    if len(data)>MAX_ROWS:
        raise ZetarisContractError('Zetaris SQL result exceeded1000 returned rows.')
    result=[]
    for values in data:
        if not isinstance(values,list) or len(values)!=len(headers):
            raise ZetarisContractError('Zetaris SQL result row width does not match its headers.')
        result.append(normalize_row(dict(zip(headers,values))))
    return result
