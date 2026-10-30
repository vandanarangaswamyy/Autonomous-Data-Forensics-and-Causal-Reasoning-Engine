# Financial reporting

Daily financial reports group staged payments by business day and region. Revenue USD
is the sum of local amount multiplied by the currency rate for that business day.
Payment count counts staged events. Daily exchange rates and staged payments are
required inputs. Monetary values use decimal arithmetic on PostgreSQL.
