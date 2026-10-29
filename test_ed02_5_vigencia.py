import unittest
from datetime import datetime, timezone, timedelta
from ed02_5_vigencia import add_months_clamped, calcular_vigencia, esta_vigente
UTC=timezone.utc
D=lambda y,m,d: datetime(y,m,d,12,30,tzinfo=UTC)

class VigenciaTests(unittest.TestCase):
    def test_mensal_fim_mes(self):
        self.assertEqual(calcular_vigencia('mensal',D(2026,1,31)).fim_exclusivo,D(2026,2,28))
    def test_bissexto(self):
        self.assertEqual(add_months_clamped(D(2028,1,31),1),D(2028,2,29))
    def test_anual_bissexto(self):
        self.assertEqual(calcular_vigencia('anual',D(2028,2,29)).fim_exclusivo,D(2029,2,28))
    def test_renovacao_antecipada(self):
        r=calcular_vigencia('mensal',D(2026,10,15),D(2026,11,1))
        self.assertTrue(r.renovacao_antecipada)
        self.assertEqual((r.inicio,r.fim_exclusivo),(D(2026,11,1),D(2026,12,1)))
    def test_expirado_reinicia(self):
        r=calcular_vigencia('mensal',D(2026,10,15),D(2026,9,1))
        self.assertFalse(r.renovacao_antecipada)
        self.assertEqual(r.inicio,D(2026,10,15))
    def test_fronteira_exclusiva(self):
        self.assertTrue(esta_vigente(D(2026,1,1),D(2026,2,1),D(2026,1,1)))
        self.assertFalse(esta_vigente(D(2026,1,1),D(2026,2,1),D(2026,2,1)))
    def test_fuso_convertido(self):
        menos3=timezone(timedelta(hours=-3))
        aprovado=datetime(2026,10,15,9,30,tzinfo=menos3)
        self.assertEqual(calcular_vigencia('mensal',aprovado).inicio,D(2026,10,15))
    def test_plano_invalido(self):
        with self.assertRaises(ValueError): calcular_vigencia('semestral',D(2026,10,15))
    def test_data_ingenua_proibida(self):
        with self.assertRaises(ValueError): calcular_vigencia('mensal',datetime(2026,10,15))
    def test_intervalo_invalido(self):
        with self.assertRaises(ValueError): esta_vigente(D(2026,2,1),D(2026,1,1),D(2026,1,15))

if __name__=='__main__': unittest.main()
