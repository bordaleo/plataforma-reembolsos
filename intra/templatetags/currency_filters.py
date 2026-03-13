from django import template

register = template.Library()


@register.filter(name='currency_br')
def currency_br(value):
    """
    Formata um valor numérico como moeda brasileira com separadores de milhar.
    Exemplo: 133511.48 -> "133.511,48"
    """
    if value is None:
        return "0,00"
    
    try:
        # Converter para float se necessário
        valor = float(value)
        
        # Formatar com 2 casas decimais
        valor_str = f"{valor:.2f}"
        
        # Separar parte inteira e decimal
        partes = valor_str.split('.')
        parte_inteira = partes[0]
        parte_decimal = partes[1] if len(partes) > 1 else "00"
        
        # Adicionar separadores de milhar (ponto)
        parte_inteira_formatada = ""
        for i, digito in enumerate(reversed(parte_inteira)):
            if i > 0 and i % 3 == 0:
                parte_inteira_formatada = "." + parte_inteira_formatada
            parte_inteira_formatada = digito + parte_inteira_formatada
        
        # Retornar no formato brasileiro: 133.511,48
        return f"{parte_inteira_formatada},{parte_decimal}"
    except (ValueError, TypeError):
        return "0,00"
